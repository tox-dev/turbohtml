/* Pythonic tree search: find()/find_all() over any axis with tag, attribute, and class
   filters, the everyday query API that pulls matching elements out of a parsed tree. */

#include "dom/nodes.h"

/* A compiled find()/find_all() query: the tag and class_ filters, the resolved
   (atom, filter) pairs for the named attribute filters, the axis to search, and
   the result cap. Every filter PyObject is borrowed from the live call. */
typedef struct {
    PyObject *tag;          /* tag filter, or NULL for no tag constraint */
    PyObject *class_filter; /* class_ filter, or NULL */
    PyObject *text;         /* text predicate over each element's collected text, or NULL */
    enum th_axis axis;
    Py_ssize_t limit; /* -1 for unlimited */
    uint32_t *atoms;  /* resolved name atoms for the attribute filters */
    PyObject **filters;
    int *filter_plain; /* per attribute filter: 1 when it is a single str */
    Py_ssize_t nattr;
    /* Fast path for a plain-string tag filter: resolve the name to an atom once
       so the per-node test is an integer compare instead of building a str for
       every element. tag_plain is set when tag is a single str; tag_atom is its
       atom, or TH_TAG_UNKNOWN for a name outside the table (then the rare
       unknown-atom elements are compared by name). */
    int tag_plain;
    uint16_t tag_atom;
    /* Fast path for a plain-string class_ filter: its code points are copied
       once so each element's class tokens are compared without building a str
       per token. class_ucs4 is NULL unless class_filter is a single str. */
    Py_UCS4 *class_ucs4;
    Py_ssize_t class_len;
} query_t;

static void free_query(query_t *query) {
    PyMem_Free(query->atoms);
    PyMem_Free(query->filters);
    PyMem_Free(query->filter_plain);
    PyMem_Free(query->class_ucs4);
}

/* Whether a node's UCS4 value equals a str filter's code points, with no
   intermediate str allocation. */
static int ucs4_equals_pystr(const Py_UCS4 *value, Py_ssize_t value_len, PyObject *filter) {
    if (value_len != PyUnicode_GET_LENGTH(filter)) {
        return 0;
    }
    int kind = PyUnicode_KIND(filter);
    const void *data = PyUnicode_DATA(filter);
    for (Py_ssize_t index = 0; index < value_len; index++) {
        if (value[index] != PyUnicode_READ(kind, data, index)) {
            return 0;
        }
    }
    return 1;
}

static int resolve_axis(module_state *state, PyObject *value, enum th_axis *out) {
    for (int index = 0; index < TH_AXIS_COUNT; index++) {
        if (value == state->axes[index]) {
            *out = (enum th_axis)index;
            return 0;
        }
    }
    PyErr_SetString(PyExc_TypeError, "axis must be an Axis member");
    return -1;
}

/* Match one filter against a candidate value: a str (a tag name or attribute
   value), Py_None (a valueless attribute), or NULL (an absent attribute).
   Returns 1/0, or -1 with an exception set. */
static int filter_matches(module_state *state, PyObject *filter, PyObject *value) {
    if (PyBool_Check(filter)) {
        int present = value != NULL;
        return filter == Py_True ? present : !present;
    }
    if (PyList_Check(filter)) {
        Py_ssize_t count = PyList_GET_SIZE(filter);
        for (Py_ssize_t index = 0; index < count; index++) {
            int matched = filter_matches(state, PyList_GET_ITEM(filter, index), value);
            if (matched != 0) {
                return matched;
            }
        }
        return 0;
    }
    if (PyUnicode_Check(filter)) {
        if (value == NULL || value == Py_None) {
            return 0;
        }
        return PyUnicode_Compare(value, filter) == 0;
    }
    if (PyObject_TypeCheck(filter, (PyTypeObject *)state->pattern_type)) {
        if (value == NULL || value == Py_None) {
            return 0;
        }
        PyObject *match = PyObject_CallMethod(filter, "search", "O", value);
        if (match == NULL) { /* GCOVR_EXCL_BR_LINE: search over a str cannot raise */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int matched = match != Py_None;
        Py_DECREF(match);
        return matched;
    }
    if (PyCallable_Check(filter)) {
        PyObject *result = PyObject_CallOneArg(filter, value != NULL ? value : Py_None);
        if (result == NULL) {
            return -1;
        }
        int truth = PyObject_IsTrue(result);
        Py_DECREF(result);
        return truth;
    }
    PyErr_SetString(PyExc_TypeError, "filter must be a str, bool, compiled regex, callable, or list");
    return -1;
}

/* The node's value for an attribute atom: a new str reference, Py_None for a
   valueless attribute (borrowed), or NULL when absent. *owned says whether the
   caller must DECREF (only the new-str case is owned). */
static PyObject *attr_value(th_node *node, uint32_t atom, int *owned) {
    *owned = 0;
    const th_node_attr *attr = find_node_attr(node, atom);
    if (attr == NULL) {
        return NULL;
    }
    if (attr->value == NULL) {
        return Py_None;
    }
    *owned = 1;
    return ucs4_to_str(attr->value, attr->value_len);
}

/* class_ matches the multi-valued class attribute: against the whole value first,
   then each whitespace-separated token. */
static int class_matches(module_state *state, th_node *node, PyObject *filter) {
    const th_node_attr *attr = find_node_attr(node, TH_ATTR_CLASS);
    if (attr == NULL || attr->value == NULL) {
        return filter_matches(state, filter, attr == NULL ? NULL : Py_None);
    }
    PyObject *whole = ucs4_to_str(attr->value, attr->value_len);
    if (whole == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int matched = filter_matches(state, filter, whole);
    Py_DECREF(whole);
    if (matched != 0) {
        return matched;
    }
    Py_ssize_t cursor = 0;
    while (cursor < attr->value_len) {
        while (cursor < attr->value_len && is_space(attr->value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < attr->value_len && !is_space(attr->value[cursor])) {
            cursor++;
        }
        if (cursor > start) {
            PyObject *token = ucs4_to_str(&attr->value[start], cursor - start);
            if (token == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            int token_matched = filter_matches(state, filter, token);
            Py_DECREF(token);
            if (token_matched != 0) {
                return token_matched;
            }
        }
    }
    return 0;
}

/* class_matches for a plain-string filter: compare the filter's code points to
   the whole class value and then each token directly, allocating nothing. */
static int class_matches_plain(th_node *node, const Py_UCS4 *want, Py_ssize_t want_len) {
    const th_node_attr *attr = find_node_attr(node, TH_ATTR_CLASS);
    if (attr == NULL || attr->value == NULL) {
        return 0; /* a string filter never matches an absent or valueless class */
    }
    size_t bytes = (size_t)want_len * sizeof(Py_UCS4);
    if (attr->value_len == want_len && memcmp(attr->value, want, bytes) == 0) {
        return 1; /* the whole value equals the filter */
    }
    Py_ssize_t cursor = 0;
    while (cursor < attr->value_len) {
        while (cursor < attr->value_len && is_space(attr->value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < attr->value_len && !is_space(attr->value[cursor])) {
            cursor++;
        }
        if (cursor - start == want_len && memcmp(&attr->value[start], want, bytes) == 0) {
            return 1;
        }
    }
    return 0;
}

/* For a plain-string tag filter, whether node's tag name matches: a known name is
   a pure integer atom compare; an unknown one can only match the rare unknown-atom
   elements, compared by name. Returns 1/0, or -1 with an exception set. */
static int tag_plain_matches(const query_t *query, th_node *node) {
    if (query->tag_atom != TH_TAG_UNKNOWN) {
        return node->atom == query->tag_atom;
    }
    if (node->atom != TH_TAG_UNKNOWN) {
        return 0;
    }
    PyObject *name = ucs4_to_str(node->text, node->text_len);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int equal = PyUnicode_Compare(name, query->tag) == 0;
    Py_DECREF(name);
    return equal;
}

/* Whether an element matches the whole query. Returns 1/0, or -1 with an
   exception set. */
static int node_matches(module_state *state, th_node *node, const query_t *query) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    if (query->tag_plain) {
        int matched = tag_plain_matches(query, node);
        if (matched <= 0) {
            return matched;
        }
    } else if (query->tag != NULL) {
        PyObject *name = ucs4_to_str(node->text, node->text_len);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int matched = filter_matches(state, query->tag, name);
        Py_DECREF(name);
        if (matched <= 0) {
            return matched;
        }
    }
    if (query->class_ucs4 != NULL) {
        if (class_matches_plain(node, query->class_ucs4, query->class_len) == 0) {
            return 0;
        }
    } else if (query->class_filter != NULL) {
        int matched = class_matches(state, node, query->class_filter);
        if (matched <= 0) {
            return matched;
        }
    }
    for (Py_ssize_t index = 0; index < query->nattr; index++) {
        if (query->filter_plain[index]) {
            /* a str filter matches only a present, valued attribute equal to it */
            const th_node_attr *attr = find_node_attr(node, query->atoms[index]);
            int equal = attr != NULL && attr->value != NULL &&
                        ucs4_equals_pystr(attr->value, attr->value_len, query->filters[index]);
            if (!equal) {
                return 0;
            }
            continue;
        }
        int owned;
        PyObject *value = attr_value(node, query->atoms[index], &owned);
        if (owned && value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int matched = filter_matches(state, query->filters[index], value);
        if (owned) {
            Py_DECREF(value);
        }
        if (matched <= 0) {
            return matched;
        }
    }
    return 1;
}

static th_node *axis_first(th_node *node, enum th_axis axis) {
    switch (axis) {
    case TH_AXIS_ANCESTORS:
        return node->parent;
    case TH_AXIS_NEXT_SIBLINGS:
        return node->next_sibling;
    case TH_AXIS_PREVIOUS_SIBLINGS:
        return node->prev_sibling;
    case TH_AXIS_FOLLOWING:
        return subtree_next(node);
    case TH_AXIS_PRECEDING:
        return preceding_skip(previous_element(node), node);
    default: /* DESCENDANTS and CHILDREN both start at the first child */
        return node->first_child;
    }
}

static th_node *axis_next(th_node *current, th_node *origin, enum th_axis axis) {
    switch (axis) {
    case TH_AXIS_CHILDREN:
    case TH_AXIS_NEXT_SIBLINGS:
        return current->next_sibling;
    case TH_AXIS_PREVIOUS_SIBLINGS:
        return current->prev_sibling;
    case TH_AXIS_ANCESTORS:
        return current->parent;
    case TH_AXIS_FOLLOWING:
        return preorder_next(current, NULL);
    case TH_AXIS_PRECEDING:
        return preceding_skip(previous_element(current), origin);
    default: /* DESCENDANTS: pre-order, bounded to the origin's subtree */
        return preorder_next(current, origin);
    }
}

static int add_attr_filter(th_tree *tree, query_t *query, PyObject *key, PyObject *filter) {
    if (!PyUnicode_Check(key)) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be a str");
        return -1;
    }
    Py_ssize_t len;
    const char *bytes = PyUnicode_AsUTF8AndSize(key, &len);
    if (bytes == NULL) { /* GCOVR_EXCL_BR_LINE: a str always encodes to UTF-8 */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    query->atoms[query->nattr] = th_attr_lookup(tree, bytes, len);
    query->filters[query->nattr] = filter;
    query->filter_plain[query->nattr] = PyUnicode_Check(filter);
    query->nattr++;
    return 0;
}

static int is_reserved_key(PyObject *key, int is_find_all) {
    return PyUnicode_CompareWithASCIIString(key, "axis") == 0 || PyUnicode_CompareWithASCIIString(key, "class_") == 0 ||
           PyUnicode_CompareWithASCIIString(key, "attrs") == 0 || PyUnicode_CompareWithASCIIString(key, "text") == 0 ||
           (is_find_all && PyUnicode_CompareWithASCIIString(key, "limit") == 0);
}

/* Compile the (tag, axis, class_, attrs, limit, **filters) arguments. Always
   leaves query in a free_query-safe state, even on error. */
static int build_query(PyObject *self, PyObject *args, PyObject *kwargs, int is_find_all, query_t *query) {
    module_state *state = state_of(self);
    th_tree *tree = tree_of(self);
    query->tag = NULL;
    query->class_filter = NULL;
    query->text = NULL;
    query->axis = TH_AXIS_DESCENDANTS;
    query->limit = -1;
    query->atoms = NULL;
    query->filters = NULL;
    query->filter_plain = NULL;
    query->nattr = 0;
    query->tag_plain = 0;
    query->tag_atom = TH_TAG_UNKNOWN;
    query->class_ucs4 = NULL;
    query->class_len = 0;

    PyObject *tag = NULL;
    if (!PyArg_ParseTuple(args, "|O:find", &tag)) {
        return -1;
    }
    if (tag != NULL && tag != Py_None) {
        query->tag = tag;
        /* a plain str tag matches by interned atom; encode case-sensitively (a
           find() name match is exact, unlike a case-insensitive CSS type) */
        if (PyUnicode_Check(tag)) {
            Py_ssize_t blen;
            const char *bytes = PyUnicode_AsUTF8AndSize(tag, &blen);
            if (bytes == NULL) {
                /* a lone surrogate cannot encode; fall back to the str-compare path */
                PyErr_Clear();
            } else {
                query->tag_plain = 1;
                query->tag_atom = blen > 0 ? th_tag_lookup(bytes, blen) : TH_TAG_UNKNOWN;
            }
        }
    }

    PyObject *attrs_dict = NULL;
    Py_ssize_t named = 0;
    PyObject *key, *value;
    Py_ssize_t pos = 0;
    while (kwargs != NULL && PyDict_Next(kwargs, &pos, &key, &value)) {
        if (PyUnicode_CompareWithASCIIString(key, "axis") == 0) {
            if (resolve_axis(state, value, &query->axis) < 0) {
                return -1;
            }
        } else if (PyUnicode_CompareWithASCIIString(key, "class_") == 0) {
            query->class_filter = value;
        } else if (PyUnicode_CompareWithASCIIString(key, "text") == 0) {
            query->text = value;
        } else if (PyUnicode_CompareWithASCIIString(key, "attrs") == 0) {
            if (!PyDict_Check(value)) {
                PyErr_SetString(PyExc_TypeError, "attrs must be a dict");
                return -1;
            }
            attrs_dict = value;
        } else if (is_find_all && PyUnicode_CompareWithASCIIString(key, "limit") == 0) {
            if (value == Py_None) {
                query->limit = -1;
            } else if (PyLong_Check(value)) {
                query->limit = PyLong_AsSsize_t(value);
                if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: an overflowing limit cannot be forced from a test */
                    return -1;          /* GCOVR_EXCL_LINE: overflow path */
                }
            } else {
                PyErr_SetString(PyExc_TypeError, "limit must be an int or None");
                return -1;
            }
        } else {
            named++;
        }
    }

    if (query->class_filter != NULL && PyUnicode_Check(query->class_filter)) {
        /* a plain str class_ filter matches by code-point compare, no per-token str */
        query->class_len = PyUnicode_GET_LENGTH(query->class_filter);
        query->class_ucs4 = PyUnicode_AsUCS4Copy(query->class_filter);
        if (query->class_ucs4 == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced from a test */
            return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }

    Py_ssize_t capacity = named + (attrs_dict != NULL ? PyDict_GET_SIZE(attrs_dict) : 0);
    if (capacity > 0) {
        query->atoms = PyMem_Malloc((size_t)capacity * sizeof(uint32_t));
        query->filters = PyMem_Malloc((size_t)capacity * sizeof(PyObject *));
        query->filter_plain = PyMem_Malloc((size_t)capacity * sizeof(int));
        /* allocation failure cannot be forced from a test */
        if (query->atoms == NULL || query->filters == NULL || query->filter_plain == NULL) { /* GCOVR_EXCL_BR_LINE */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    pos = 0;
    while (kwargs != NULL && PyDict_Next(kwargs, &pos, &key, &value)) {
        if (is_reserved_key(key, is_find_all)) {
            continue;
        }
        if (add_attr_filter(tree, query, key, value) < 0) { /* GCOVR_EXCL_BR_LINE: a **kwargs key is always a str */
            return -1; /* GCOVR_EXCL_LINE: add_attr_filter only fails on a non-str name, impossible here */
        }
    }
    pos = 0;
    while (attrs_dict != NULL && PyDict_Next(attrs_dict, &pos, &key, &value)) {
        if (add_attr_filter(tree, query, key, value) < 0) {
            return -1;
        }
    }
    return 0;
}

/* Whether the query is a plain known tag with no other constraint on the
   descendant axis: the shape the tag-only fast path handles with a pre-order
   walk and an integer atom compare, skipping the general matcher. */
static int query_is_simple_tag(const query_t *query) {
    return query->tag_plain && query->tag_atom != TH_TAG_UNKNOWN && query->nattr == 0 && query->class_ucs4 == NULL &&
           query->class_filter == NULL && query->axis == TH_AXIS_DESCENDANTS;
}

/* The cheap, pure-C structural prefilter for the text-search path: whether node
   could match before its collected text is tested, deciding which elements get
   their text gathered in the snapshot pass. Only the allocation-free checks live
   here (the element type, a known plain tag, a plain class); the remaining filters,
   including any regex/callable that would need Python, are deferred to node_matches
   in the post-snapshot pass. */
static int text_candidate(const query_t *query, th_node *node) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    if (query->tag_plain && query->tag_atom != TH_TAG_UNKNOWN && node->atom != query->tag_atom) {
        return 0;
    }
    if (query->class_ucs4 != NULL && class_matches_plain(node, query->class_ucs4, query->class_len) == 0) {
        return 0;
    }
    return 1;
}

/* Snapshot, under the handle's critical section, the elements that satisfy the tag,
   class, and attribute filters together with their collected text. node_matches runs
   the structural filters here, the same way the non-text path does inside the lock;
   only the text predicate is deferred, because it may run arbitrary Python and so
   suspend the lock. text_candidate counts an upper bound first so the arrays are
   sized once with no reallocation, and it never invokes a structural callable twice.
   Writes the matched element pointers to *out_nodes and their text to *out_texts
   (both PyMem arrays the caller always frees, with *out_count entries to DECREF in
   out_texts) and the count to *out_count. Returns 0, or -1 with an exception set
   when a structural filter raised; the partial snapshot is still returned for the
   caller to release. */
static int snapshot_text_candidates(PyObject *self, const query_t *query, th_node ***out_nodes, PyObject ***out_texts,
                                    Py_ssize_t *out_count) {
    module_state *state = state_of(self);
    th_node *origin = ((NodeObject *)self)->node;
    th_tree *tree = tree_of(self);
    th_node **nodes = NULL;
    PyObject **texts = NULL;
    Py_ssize_t filled = 0;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    Py_ssize_t capacity = 0;
    for (th_node *node = axis_first(origin, query->axis); node != NULL; node = axis_next(node, origin, query->axis)) {
        if (text_candidate(query, node)) {
            capacity++;
        }
    }
    if (capacity > 0) {
        nodes = PyMem_Malloc((size_t)capacity * sizeof(th_node *));
        texts = PyMem_Malloc((size_t)capacity * sizeof(PyObject *));
        if (nodes == NULL || texts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced from a test */
            error = 1;                        /* GCOVR_EXCL_LINE: allocation-failure path */
        } else {                              /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            for (th_node *node = axis_first(origin, query->axis); node != NULL;
                 node = axis_next(node, origin, query->axis)) {
                if (!text_candidate(query, node)) {
                    continue;
                }
                int matched = node_matches(state, node, query);
                if (matched < 0) {
                    error = 1;
                    break;
                }
                if (matched == 0) {
                    continue;
                }
                PyObject *text = str_from_accessor(th_node_text, tree, node);
                if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    error = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;          /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                nodes[filled] = node;
                texts[filled] = text;
                filled++;
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    *out_nodes = nodes;
    *out_texts = texts;
    *out_count = filled;
    return error ? -1 : 0;
}

/* Search along the axis for elements whose collected text satisfies the text
   predicate, composed with the tag, class, and attribute filters. The predicate
   (a str, compiled regex, or callable) runs over the snapshot taken under the lock;
   nodes are arena-allocated and never freed individually, so the snapshot's
   pointers stay valid across the predicate even if a concurrent mutation rewires
   the tree. Returns the first match (want_all 0, None when nothing matches) or the
   list of matches up to the limit (want_all 1). */
static PyObject *find_with_text(PyObject *self, const query_t *query, int want_all) {
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node **nodes;
    PyObject **texts;
    Py_ssize_t count;
    int error = snapshot_text_candidates(self, query, &nodes, &texts, &count) < 0;
    PyObject *result = NULL;
    if (!error && want_all) {
        result = PyList_New(0);
        if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            error = 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    }
    th_node *found = NULL;
    for (Py_ssize_t index = 0; !error && index < count; index++) {
        if (want_all && query->limit >= 0 && PyList_GET_SIZE(result) >= query->limit) {
            break;
        }
        int matched = filter_matches(state, query->text, texts[index]);
        if (matched < 0) {
            error = 1;
            break;
        }
        if (matched == 0) {
            continue;
        }
        if (!want_all) {
            found = nodes[index];
            break;
        }
        if (append_wrapped(result, state, handle, nodes[index]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc cannot fail */
            error = 1;                                                 /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_DECREF(texts[index]);
    }
    PyMem_Free(nodes);
    PyMem_Free(texts);
    if (error) {
        Py_XDECREF(result);
        return NULL;
    }
    if (want_all) {
        return result;
    }
    return node_wrap(state, handle, found);
}

PyObject *node_find(PyObject *self, PyObject *args, PyObject *kwargs) {
    query_t query;
    if (build_query(self, args, kwargs, 0, &query) < 0) {
        free_query(&query);
        return NULL;
    }
    if (query.text != NULL) {
        PyObject *result = find_with_text(self, &query, 0);
        free_query(&query);
        return result;
    }
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    th_node *found = NULL;
    int error = 0;
    /* hold the per-tree lock across the walk so a concurrent extract() cannot rewire
       the child/sibling pointers mid-read (a no-op on the GIL build) */
    Py_BEGIN_CRITICAL_SECTION(handle);
    HandleObject *handle_obj = (HandleObject *)handle;
    if (handle_use_index(handle_obj, origin, query_is_simple_tag(&query))) {
        if (handle_obj->index_offsets[query.tag_atom + 1] > handle_obj->index_offsets[query.tag_atom]) {
            found = handle_obj->index_nodes[handle_obj->index_offsets[query.tag_atom]];
        }
    } else if (query_is_simple_tag(&query)) {
        /* the first element whose atom matches, found with an integer compare */
        for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
            if (node->atom == query.tag_atom) {
                found = node;
                break;
            }
        }
    } else {
        for (th_node *node = axis_first(origin, query.axis); node != NULL; node = axis_next(node, origin, query.axis)) {
            int matched = node_matches(state, node, &query);
            if (matched < 0) {
                error = 1;
                break;
            }
            if (matched) {
                found = node;
                break;
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    free_query(&query);
    if (error) {
        return NULL;
    }
    return node_wrap(state, ((NodeObject *)self)->handle, found);
}

PyObject *node_find_all(PyObject *self, PyObject *args, PyObject *kwargs) {
    query_t query;
    if (build_query(self, args, kwargs, 1, &query) < 0) {
        free_query(&query);
        return NULL;
    }
    if (query.text != NULL) {
        PyObject *result = find_with_text(self, &query, 1);
        free_query(&query);
        return result;
    }
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    PyObject *out = PyList_New(0);
    if (out == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        free_query(&query); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int error = 0;
    /* a non-element node carries TH_TAG_UNKNOWN, so the atom compare alone
       selects the right elements on the fast path */
    /* hold the per-tree lock across the walk so a concurrent extract() cannot rewire
       the child/sibling pointers mid-read (a no-op on the GIL build) */
    Py_BEGIN_CRITICAL_SECTION(handle);
    HandleObject *handle_obj = (HandleObject *)handle;
    if (handle_use_index(handle_obj, origin, query_is_simple_tag(&query))) {
        Py_ssize_t end = handle_obj->index_offsets[query.tag_atom + 1];
        for (Py_ssize_t pos = handle_obj->index_offsets[query.tag_atom]; pos < end; pos++) {
            if (query.limit >= 0 && PyList_GET_SIZE(out) >= query.limit) {
                break;
            }
            if (append_wrapped(out, state, handle, handle_obj->index_nodes[pos]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                break;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    } else if (query_is_simple_tag(&query)) {
        for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
            if (query.limit >= 0 && PyList_GET_SIZE(out) >= query.limit) {
                break;
            }
            if (node->atom != query.tag_atom) {
                continue;
            }
            if (append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                error = 1;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    } else {
        for (th_node *node = axis_first(origin, query.axis); node != NULL; node = axis_next(node, origin, query.axis)) {
            if (query.limit >= 0 && PyList_GET_SIZE(out) >= query.limit) {
                break;
            }
            int matched = node_matches(state, node, &query);
            if (matched < 0) {
                error = 1;
                break;
            }
            if (matched && append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc cannot fail */
                error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                break;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    free_query(&query);
    if (error) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}
